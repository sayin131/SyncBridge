package com.syncbridge.app

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** In-app activity log + "is a sync running" flag, shared by the UI and the background worker. */
object SyncLog {
    private const val MAX_LINES = 200
    private val _lines = MutableStateFlow<List<String>>(emptyList())
    val lines: StateFlow<List<String>> = _lines.asStateFlow()
    private val _running = MutableStateFlow(false)
    val running: StateFlow<Boolean> = _running.asStateFlow()
    private var file: File? = null

    fun init(ctx: Context) {
        if (file != null) return
        val f = File(ctx.filesDir, "activity.log")
        file = f
        _lines.value = runCatching { f.readLines().takeLast(MAX_LINES) }.getOrDefault(emptyList())
    }

    @Synchronized
    fun add(msg: String) {
        val line = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date()) + "  " + msg
        _lines.update { (it + line).takeLast(MAX_LINES) }
        runCatching {
            val f = file ?: return@runCatching
            if (f.length() > 100_000) f.writeText(_lines.value.joinToString("\n") + "\n") else f.appendText(line + "\n")
        }
    }

    fun setRunning(value: Boolean) {
        _running.value = value
    }
}
