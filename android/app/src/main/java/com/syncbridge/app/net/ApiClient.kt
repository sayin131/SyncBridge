package com.syncbridge.app.net

import com.syncbridge.app.FileMeta
import com.syncbridge.app.PlanAction
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Blocking HTTP client for the SyncBridge protocol (see protocol/protocol.md). Call from Dispatchers.IO. */
class ApiClient(
    private val ip: String,
    private val port: Int,
    fp: String,
    private val deviceId: String? = null,
    private val token: String? = null,
) {
    private val http = Pinning.client(fp)
    private val json = "application/json".toMediaType()
    private val octet = "application/octet-stream".toMediaType()

    private fun url(path: String, query: Map<String, String> = emptyMap()): HttpUrl {
        val b = HttpUrl.Builder().scheme("https").host(ip).port(port).addPathSegments(path.trimStart('/'))
        query.forEach { (k, v) -> b.addQueryParameter(k, v) }
        return b.build()
    }

    private fun request(url: HttpUrl): Request.Builder {
        val b = Request.Builder().url(url)
        if (deviceId != null && token != null) {
            b.header("X-Device-Id", deviceId)
            b.header("Authorization", "Bearer $token")
        }
        return b
    }

    private fun execJson(r: Request): JSONObject =
        http.newCall(r).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) throw IOException("HTTP ${resp.code}: $body")
            JSONObject(body)
        }

    private fun jsonBody(o: JSONObject) = o.toString().toRequestBody(json)

    fun ping() {
        execJson(request(url("ping")).get().build())
    }

    fun pair(deviceName: String, secret: String, newDeviceId: String): String {
        val body = JSONObject().put("device_id", newDeviceId).put("device_name", deviceName).put("secret", secret)
        return execJson(request(url("pair")).post(jsonBody(body)).build()).getString("token")
    }

    fun plan(manifest: Map<String, FileMeta>): List<PlanAction> {
        val m = JSONObject()
        manifest.forEach { (p, f) ->
            m.put(p, JSONObject().put("size", f.size).put("mtime", f.mtime).put("sha256", f.sha256))
        }
        val resp = execJson(request(url("sync/plan")).post(jsonBody(JSONObject().put("phone_manifest", m))).build())
        val arr = resp.getJSONArray("actions")
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            PlanAction(
                kind = o.getString("kind"),
                path = o.getString("path"),
                sha256 = if (o.has("sha256")) o.getString("sha256") else null,
                size = if (o.has("size")) o.getLong("size") else null,
            )
        }
    }

    /** Caller must close the returned Response. */
    fun download(path: String, offset: Long): Response =
        http.newCall(request(url("sync/download/$path")).header("Range", "bytes=$offset-").get().build()).execute()

    fun uploadStatus(path: String, sha: String): Long =
        execJson(request(url("sync/upload-status/$path", mapOf("sha256" to sha))).get().build()).getLong("received_bytes")

    fun uploadChunk(path: String, sha: String, offset: Long, data: ByteArray, len: Int) {
        val body = data.toRequestBody(octet, 0, len)
        val u = url("sync/upload/$path", mapOf("sha256" to sha, "offset" to offset.toString()))
        execJson(request(u).put(body).build())
    }

    fun uploadComplete(path: String, sha: String) {
        execJson(request(url("sync/upload-complete/$path")).post(jsonBody(JSONObject().put("sha256", sha))).build())
    }

    fun delete(path: String) {
        execJson(request(url("sync/delete")).post(jsonBody(JSONObject().put("path", path))).build())
    }

    fun ack(path: String, sha: String?) {
        val body = JSONObject().put("path", path).put("sha256", sha ?: JSONObject.NULL)
        execJson(request(url("sync/ack")).post(jsonBody(body)).build())
    }

    /** Authenticated live-event socket (file_changed notifications). */
    fun openEvents(listener: WebSocketListener): WebSocket {
        val wsHttp = http.newBuilder().readTimeout(0, TimeUnit.MILLISECONDS).pingInterval(30, TimeUnit.SECONDS).build()
        return wsHttp.newWebSocket(request(url("events")).build(), listener)
    }
}
