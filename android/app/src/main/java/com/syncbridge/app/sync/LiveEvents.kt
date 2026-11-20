package com.syncbridge.app.sync

import android.content.Context
import com.syncbridge.app.data.Store
import com.syncbridge.app.net.ApiClient
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/**
 * While the app is open and auto-sync is on, listens to the laptop's authenticated
 * WebSocket; a "file_changed" message triggers a (debounced) sync. When the app is closed,
 * the periodic WorkManager job takes over (Android doesn't allow permanent background sockets).
 */
class LiveEvents(private val ctx: Context, private val store: Store, private val scope: CoroutineScope) {
    private var loop: Job? = null
    private var debounce: Job? = null

    fun start() {
        if (loop?.isActive == true) return
        loop = scope.launch(Dispatchers.IO) {
            while (isActive) {
                if (store.isPaired && store.autoSync) {
                    try {
                        connectOnce()
                    } catch (_: Exception) {
                    }
                }
                delay(10_000)  // reconnect backoff
            }
        }
    }

    fun stop() {
        loop?.cancel()
        debounce?.cancel()
    }

    private suspend fun connectOnce() {
        val closed = CompletableDeferred<Unit>()
        val api = ApiClient(store.ip!!, store.port, store.fp!!, store.deviceId, store.token)
        val socket = api.openEvents(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("file_changed")) scheduleSync()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                closed.complete(Unit)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                closed.complete(Unit)
            }
        })
        try {
            closed.await()
        } finally {
            socket.cancel()
        }
    }

    private fun scheduleSync() {
        debounce?.cancel()
        debounce = scope.launch {
            delay(3_000)  // several events often arrive for one save
            Scheduler.syncNow(ctx)
        }
    }
}
