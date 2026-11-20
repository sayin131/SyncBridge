package com.syncbridge.app

import android.app.Application

class SyncBridgeApp : Application() {
    override fun onCreate() {
        super.onCreate()
        SyncLog.init(this)
    }
}
