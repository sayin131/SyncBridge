package com.syncbridge.app.data

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** Pairing credentials and settings, encrypted at rest with a key held in the Android Keystore. */
class Store(ctx: Context) {
    private val prefs = EncryptedSharedPreferences.create(
        ctx,
        "syncbridge_secure",
        MasterKey.Builder(ctx).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    var ip: String?
        get() = prefs.getString("ip", null)
        set(v) = prefs.edit().putString("ip", v).apply()
    var port: Int
        get() = prefs.getInt("port", 8443)
        set(v) = prefs.edit().putInt("port", v).apply()
    val fp: String? get() = prefs.getString("fp", null)
    val deviceId: String? get() = prefs.getString("device_id", null)
    val token: String? get() = prefs.getString("token", null)
    var treeUri: String?
        get() = prefs.getString("tree_uri", null)
        set(v) = prefs.edit().putString("tree_uri", v).apply()
    var autoSync: Boolean
        get() = prefs.getBoolean("auto_sync", false)
        set(v) = prefs.edit().putBoolean("auto_sync", v).apply()

    val isPaired: Boolean get() = ip != null && fp != null && deviceId != null && token != null

    fun savePairing(ip: String, port: Int, fp: String, deviceId: String, token: String) {
        prefs.edit().putString("ip", ip).putInt("port", port).putString("fp", fp)
            .putString("device_id", deviceId).putString("token", token).apply()
    }

    fun clear() = prefs.edit().clear().apply()
}
