package com.syncbridge.app.sync

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import android.webkit.MimeTypeMap
import androidx.documentfile.provider.DocumentFile
import java.io.File
import java.io.InputStream

data class LocalFile(val rel: String, val uri: Uri, val size: Long, val mtime: Long)

/**
 * The synced folder on the phone, accessed through Android's Storage Access Framework
 * (the folder the user picked). Scoped Storage forbids plain file paths, so everything
 * goes through content URIs.
 */
class SafTree(private val ctx: Context, private val treeUri: Uri) {
    private val cr = ctx.contentResolver

    /** Fast recursive listing using cursor queries (DocumentFile.listFiles is far slower). */
    fun walk(): List<LocalFile> {
        val out = mutableListOf<LocalFile>()
        walkDir(DocumentsContract.getTreeDocumentId(treeUri), "", out)
        return out
    }

    private fun walkDir(docId: String, prefix: String, out: MutableList<LocalFile>) {
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, docId)
        val projection = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_SIZE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
        )
        val subdirs = mutableListOf<Pair<String, String>>()
        cr.query(children, projection, null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val id = c.getString(0)
                val name = c.getString(1) ?: continue
                val mime = c.getString(2)
                val rel = if (prefix.isEmpty()) name else "$prefix/$name"
                if (mime == DocumentsContract.Document.MIME_TYPE_DIR) {
                    subdirs += id to rel
                } else if (!name.endsWith(".part")) {
                    out += LocalFile(rel, DocumentsContract.buildDocumentUriUsingTree(treeUri, id), c.getLong(3), c.getLong(4))
                }
            }
        }
        for ((id, rel) in subdirs) walkDir(id, rel, out)
    }

    fun open(uri: Uri): InputStream = cr.openInputStream(uri) ?: error("cannot open $uri")

    fun find(rel: String): DocumentFile? {
        var cur = DocumentFile.fromTreeUri(ctx, treeUri) ?: return null
        for (seg in rel.split("/")) cur = cur.findFile(seg) ?: return null
        return cur
    }

    private fun createOrGet(rel: String): DocumentFile {
        val segs = rel.split("/")
        var cur = DocumentFile.fromTreeUri(ctx, treeUri) ?: error("folder not accessible")
        for (seg in segs.dropLast(1)) {
            cur = cur.findFile(seg)?.takeIf { it.isDirectory } ?: cur.createDirectory(seg) ?: error("cannot create folder $seg")
        }
        val name = segs.last()
        cur.findFile(name)?.let { return it }
        val ext = name.substringAfterLast('.', "")
        val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext.lowercase()) ?: "application/octet-stream"
        return cur.createFile(mime, name) ?: error("cannot create $name")
    }

    /** Copy a fully downloaded and verified temp file into the synced folder. */
    fun write(rel: String, source: File) {
        val doc = createOrGet(rel)
        val out = cr.openOutputStream(doc.uri, "wt") ?: error("cannot write $rel")
        out.use { o -> source.inputStream().use { it.copyTo(o) } }
    }

    fun delete(rel: String) {
        find(rel)?.delete()
    }

    /** Rename within the same folder. */
    fun rename(rel: String, newLeafName: String) {
        val doc = find(rel) ?: error("missing $rel")
        if (!doc.renameTo(newLeafName)) error("rename failed for $rel")
    }
}
