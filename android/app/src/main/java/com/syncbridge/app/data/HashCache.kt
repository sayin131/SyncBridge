package com.syncbridge.app.data

import android.content.Context
import org.json.JSONObject
import java.io.File

data class CachedHash(val size: Long, val mtime: Long, val sha: String)

/** Remembers file hashes by (size, mtime) so unchanged files aren't re-hashed on every sync (saves battery). */
object HashCache {
    private fun file(ctx: Context) = File(ctx.filesDir, "hash_cache.json")

    fun load(ctx: Context): Map<String, CachedHash> = runCatching {
        val o = JSONObject(file(ctx).readText())
        o.keys().asSequence().associateWith { k ->
            val e = o.getJSONObject(k)
            CachedHash(e.getLong("size"), e.getLong("mtime"), e.getString("sha"))
        }
    }.getOrDefault(emptyMap())

    fun save(ctx: Context, cache: Map<String, CachedHash>) {
        val o = JSONObject()
        cache.forEach { (k, v) -> o.put(k, JSONObject().put("size", v.size).put("mtime", v.mtime).put("sha", v.sha)) }
        file(ctx).writeText(o.toString())
    }
}
