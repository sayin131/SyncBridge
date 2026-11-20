package com.syncbridge.app

import android.app.Application
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.documentfile.provider.DocumentFile
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.syncbridge.app.data.Store
import com.syncbridge.app.net.ApiClient
import com.syncbridge.app.sync.LiveEvents
import com.syncbridge.app.sync.Scheduler
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.UUID

data class UiState(
    val paired: Boolean = false,
    val laptop: String = "",
    val folder: String? = null,
    val autoSync: Boolean = false,
    val message: String? = null,
)

class MainViewModel(private val app: Application) : AndroidViewModel(app) {
    private val store = Store(app)
    private val live = LiveEvents(app, store, viewModelScope)

    private val _ui = MutableStateFlow(snapshot())
    val ui: StateFlow<UiState> = _ui.asStateFlow()
    val log = SyncLog.lines
    val running = SyncLog.running

    init {
        if (store.isPaired && store.autoSync) live.start()
    }

    private fun snapshot(message: String? = null) = UiState(
        paired = store.isPaired,
        laptop = if (store.isPaired) "${store.ip}:${store.port}" else "",
        folder = store.treeUri?.let { DocumentFile.fromTreeUri(app, Uri.parse(it))?.name },
        autoSync = store.autoSync,
        message = message,
    )

    private fun refresh(message: String? = null) {
        _ui.value = snapshot(message)
    }

    /** QR payload: {"ip","port","fp","secret","ttl"} produced by the PC app. */
    fun onQr(text: String) {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val j = JSONObject(text)
                val ip = j.getString("ip")
                val port = j.getInt("port")
                val fp = j.getString("fp")
                val secret = j.getString("secret")
                val deviceId = "phone-" + UUID.randomUUID().toString().take(8)
                // The TLS handshake itself enforces the fingerprint from the QR code.
                val token = ApiClient(ip, port, fp).pair(Build.MODEL, secret, deviceId)
                store.savePairing(ip, port, fp, deviceId, token)
                SyncLog.add("Paired with laptop at $ip")
                refresh("Paired! Now choose a folder to sync.")
            } catch (e: Exception) {
                refresh("Pairing failed: ${e.message}")
            }
        }
    }

    fun onFolder(uri: Uri) {
        app.contentResolver.takePersistableUriPermission(
            uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
        )
        store.treeUri = uri.toString()
        SyncLog.add("Sync folder set")
        refresh()
    }

    fun syncNow() {
        if (!store.isPaired) return refresh("Pair with your laptop first")
        if (store.treeUri == null) return refresh("Choose a folder first")
        refresh()
        Scheduler.syncNow(app)
    }

    fun setAutoSync(enabled: Boolean) {
        store.autoSync = enabled
        if (enabled) {
            Scheduler.enableAuto(app)
            live.start()
        } else {
            Scheduler.disableAuto(app)
            live.stop()
        }
        refresh()
    }

    fun unpair() {
        live.stop()
        Scheduler.disableAuto(app)
        store.clear()
        SyncLog.add("Unpaired")
        refresh()
    }

    fun dismissMessage() = refresh()

    override fun onCleared() {
        live.stop()
    }
}
