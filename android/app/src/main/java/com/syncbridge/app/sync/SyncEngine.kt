package com.syncbridge.app.sync

import android.content.Context
import android.net.Uri
import com.syncbridge.app.FileMeta
import com.syncbridge.app.PlanAction
import com.syncbridge.app.SyncLog
import com.syncbridge.app.SyncResult
import com.syncbridge.app.data.CachedHash
import com.syncbridge.app.data.HashCache
import com.syncbridge.app.data.Store
import com.syncbridge.app.net.ApiClient
import com.syncbridge.app.net.Discovery
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * One full sync cycle, mirroring phone_sim/client.py:
 *   find laptop -> scan folder -> ask laptop for a plan -> execute each action.
 *
 * Plan vocabulary (from the laptop's point of view):
 *   PUSH           laptop has the newer/new file  -> phone downloads it
 *   PULL           phone has the newer/new file   -> phone uploads it
 *   DELETE_LAPTOP  laptop deleted it              -> phone deletes its copy
 *   DELETE_PHONE   phone deleted it               -> phone tells laptop to delete
 *   CONFLICT       both changed                   -> converging "keep both" policy
 */
class SyncEngine(private val ctx: Context, private val store: Store) {
    companion object {
        private val mutex = Mutex()
        private const val CHUNK = 4 * 1024 * 1024
    }

    private fun log(msg: String) = SyncLog.add(msg)

    suspend fun run(): SyncResult {
        if (!mutex.tryLock()) return SyncResult(0, listOf("A sync is already running"))
        SyncLog.setRunning(true)
        try {
            return withContext(Dispatchers.IO) {
                try {
                    val api = connect()
                    val tree = SafTree(ctx, Uri.parse(store.treeUri ?: error("No folder chosen")))
                    val errors = mutableListOf<String>()
                    var total = 0
                    var again: Boolean
                    var pass = 0
                    do {
                        val (n, needAnother) = pass(api, tree, errors)
                        total += n
                        again = needAnother
                        pass++
                    } while (again && pass < 2)   // second pass uploads conflict copies
                    log(if (total == 0) "Already in sync" else "Sync finished: $total action(s), ${errors.size} error(s)")
                    SyncResult(total, errors)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    log("Sync failed: ${e.message}")
                    SyncResult(0, listOf(e.message ?: "unknown error"))
                }
            }
        } finally {
            SyncLog.setRunning(false)
            mutex.unlock()
        }
    }

    private suspend fun connect(): ApiClient {
        fun apiFor() = ApiClient(store.ip!!, store.port, store.fp!!, store.deviceId, store.token)
        var api = apiFor()
        try {
            api.ping()
            return api
        } catch (e: IOException) {
            log("Laptop not reachable at ${store.ip}, searching the network…")
        }
        val found = Discovery.find(ctx) ?: throw IOException("Laptop not found on this network (is the PC app running?)")
        store.ip = found.first
        store.port = found.second
        api = apiFor()
        api.ping()  // throws if the certificate pin doesn't match
        log("Found laptop at ${found.first}")
        return api
    }

    private fun pass(api: ApiClient, tree: SafTree, errors: MutableList<String>): Pair<Int, Boolean> {
        // ---- scan (hash only files whose size/mtime changed) ----
        val cache = HashCache.load(ctx)
        val files = tree.walk()
        val byPath = files.associateBy { it.rel }
        val manifest = LinkedHashMap<String, FileMeta>()
        val newCache = HashMap<String, CachedHash>()
        for (f in files) {
            val c = cache[f.rel]
            val sha = if (c != null && c.size == f.size && c.mtime == f.mtime) c.sha
            else tree.open(f.uri).use { sha256(it) }
            manifest[f.rel] = FileMeta(f.size, f.mtime / 1000.0, sha)
            newCache[f.rel] = CachedHash(f.size, f.mtime, sha)
        }
        HashCache.save(ctx, newCache)

        // ---- plan + execute ----
        val actions = api.plan(manifest)
        var needAnotherPass = false
        for (a in actions) {
            try {
                when (a.kind) {
                    "PUSH" -> {
                        val sha = download(api, tree, a)
                        api.ack(a.path, sha)
                        log("↓ ${a.path}")
                    }
                    "PULL" -> {
                        upload(api, tree, a.path, manifest.getValue(a.path).sha256, byPath.getValue(a.path))
                        log("↑ ${a.path}")
                    }
                    "DELETE_LAPTOP" -> {
                        tree.delete(a.path)
                        api.ack(a.path, null)
                        log("✕ deleted here (deleted on laptop): ${a.path}")
                    }
                    "DELETE_PHONE" -> {
                        api.delete(a.path)
                        log("✕ deleted on laptop (deleted here): ${a.path}")
                    }
                    "CONFLICT" -> needAnotherPass = resolveConflict(api, tree, a, manifest, byPath) || needAnotherPass
                    else -> log("Unknown action ${a.kind} for ${a.path}")
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                errors += "${a.kind} ${a.path}: ${e.message}"
                log("✗ ${a.kind} ${a.path}: ${e.message}")
            }
        }
        return actions.size to needAnotherPass
    }

    /**
     * Converging conflict policy (the laptop's version keeps the original name):
     *  laptop deleted + phone edited -> phone's version wins (re-uploaded), nothing lost
     *  phone deleted + laptop edited -> laptop's version is restored here, nothing lost
     *  both changed                  -> phone's file renamed "name (phone conflict …).ext",
     *                                   laptop's downloaded under the original name; the renamed
     *                                   copy uploads on the next pass so both sides keep both.
     */
    private fun resolveConflict(
        api: ApiClient, tree: SafTree, a: PlanAction,
        manifest: Map<String, FileMeta>, byPath: Map<String, LocalFile>,
    ): Boolean {
        val local = byPath[a.path]
        when {
            a.sha256 == null && local != null -> {
                upload(api, tree, a.path, manifest.getValue(a.path).sha256, local)
                log("⚠ conflict ${a.path}: laptop deleted it but you edited it; your version kept")
                return false
            }
            local == null -> {
                val sha = download(api, tree, a)
                api.ack(a.path, sha)
                log("⚠ conflict ${a.path}: restored the laptop's edited version")
                return false
            }
            else -> {
                val stamp = SimpleDateFormat("yyyy-MM-dd_HHmmss", Locale.US).format(Date())
                val leaf = a.path.substringAfterLast('/')
                val dot = leaf.lastIndexOf('.')
                val renamed = if (dot > 0) "${leaf.substring(0, dot)} (phone conflict $stamp)${leaf.substring(dot)}"
                else "$leaf (phone conflict $stamp)"
                tree.rename(a.path, renamed)
                val sha = download(api, tree, a)
                api.ack(a.path, sha)
                log("⚠ conflict ${a.path}: kept both. Yours is now \"$renamed\"")
                return true
            }
        }
    }

    // ---------- transfers ----------

    private fun download(api: ApiClient, tree: SafTree, a: PlanAction): String {
        // The temp file name includes the expected hash, so a partial from an older version
        // of the file is never resumed into a newer one.
        val tmp = File(ctx.cacheDir, "dl_" + sha256(("${a.path}|${a.sha256}").byteInputStream()).take(24) + ".part")
        var offset = if (tmp.exists()) tmp.length() else 0L
        if (a.size != null && offset > a.size) {
            tmp.delete()
            offset = 0
        }
        if (a.size == null || offset < a.size) {
            api.download(a.path, offset).use { resp ->
                if (!resp.isSuccessful) throw IOException("download failed: HTTP ${resp.code}")
                val append = offset > 0 && resp.code == 206
                FileOutputStream(tmp, append).use { out -> resp.body!!.byteStream().copyTo(out, 64 * 1024) }
            }
        }
        if (!tmp.exists()) tmp.createNewFile()  // empty file
        val sha = tmp.inputStream().use { sha256(it) }
        if (a.sha256 != null && sha != a.sha256) {
            tmp.delete()
            throw IOException("hash mismatch after download; discarded, will retry next sync")
        }
        tree.write(a.path, tmp)
        tmp.delete()
        return sha
    }

    private fun upload(api: ApiClient, tree: SafTree, path: String, sha: String, local: LocalFile) {
        // The server keys partial uploads by this exact hash, so resuming is always safe.
        var offset = api.uploadStatus(path, sha)
        if (offset > local.size) offset = 0
        tree.open(local.uri).use { ins ->
            skipFully(ins, offset)
            val buf = ByteArray(CHUNK)
            while (offset < local.size) {
                val n = readFully(ins, buf)
                if (n <= 0) break
                api.uploadChunk(path, sha, offset, buf, n)
                offset += n
            }
        }
        api.uploadComplete(path, sha)
    }

    // ---------- helpers ----------

    private fun sha256(input: InputStream): String {
        val md = MessageDigest.getInstance("SHA-256")
        val buf = ByteArray(1024 * 1024)
        while (true) {
            val n = input.read(buf)
            if (n < 0) break
            md.update(buf, 0, n)
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    private fun readFully(ins: InputStream, buf: ByteArray): Int {
        var total = 0
        while (total < buf.size) {
            val n = ins.read(buf, total, buf.size - total)
            if (n < 0) break
            total += n
        }
        return total
    }

    private fun skipFully(ins: InputStream, count: Long) {
        var left = count
        while (left > 0) {
            val s = ins.skip(left)
            if (s > 0) left -= s else if (ins.read() == -1) break else left--
        }
    }
}
