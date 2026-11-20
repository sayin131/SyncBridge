package com.syncbridge.app.sync

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.ForegroundInfo
import androidx.work.WorkerParameters
import com.syncbridge.app.data.Store

/** Runs one sync cycle in the background (periodic on Wi-Fi, or on demand). */
class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val store = Store(applicationContext)
        if (!store.isPaired || store.treeUri == null) return Result.success()
        // Showing a notification keeps long transfers alive; Android 12+ may refuse from the background.
        try {
            setForeground(foregroundInfo("Syncing with your laptop…"))
        } catch (_: Exception) {
        }
        val result = SyncEngine(applicationContext, store).run()
        return if (result.errors.isEmpty()) Result.success() else Result.retry()
    }

    override suspend fun getForegroundInfo(): ForegroundInfo = foregroundInfo("Syncing with your laptop…")

    private fun foregroundInfo(text: String): ForegroundInfo {
        val channelId = "sync"
        val nm = applicationContext.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(channelId, "Sync", NotificationManager.IMPORTANCE_LOW))
        val notification = NotificationCompat.Builder(applicationContext, channelId)
            .setContentTitle("SyncBridge")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .build()
        return if (Build.VERSION.SDK_INT >= 29) {
            ForegroundInfo(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            ForegroundInfo(1, notification)
        }
    }
}
