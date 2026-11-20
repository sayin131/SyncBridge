package com.syncbridge.app.net

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.net.Inet4Address
import kotlin.coroutines.resume

/**
 * Finds the laptop again via mDNS (_syncbridge._tcp) when its IP has changed.
 * Anything found is only a *candidate address*: the pinned-certificate check in [Pinning]
 * still decides whether we trust it, so a spoofed mDNS answer can't impersonate the laptop.
 * Many college networks block multicast; if so, re-scan the QR code.
 */
object Discovery {
    suspend fun find(ctx: Context, timeoutMs: Long = 6000): Pair<String, Int>? =
        withTimeoutOrNull(timeoutMs) {
            suspendCancellableCoroutine<Pair<String, Int>?> { cont ->
                val nsd = ctx.getSystemService(Context.NSD_SERVICE) as NsdManager
                lateinit var discovery: NsdManager.DiscoveryListener

                fun stop() {
                    try { nsd.stopServiceDiscovery(discovery) } catch (_: Exception) {}
                }

                @Suppress("DEPRECATION")
                val resolver = object : NsdManager.ResolveListener {
                    override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {}
                    override fun onServiceResolved(info: NsdServiceInfo) {
                        val host = info.host
                        if (host is Inet4Address && cont.isActive) {
                            cont.resume(host.hostAddress!! to info.port)
                        }
                    }
                }

                discovery = object : NsdManager.DiscoveryListener {
                    override fun onDiscoveryStarted(serviceType: String) {}
                    override fun onDiscoveryStopped(serviceType: String) {}
                    override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                        if (cont.isActive) cont.resume(null)
                    }
                    override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
                    override fun onServiceLost(info: NsdServiceInfo) {}
                    @Suppress("DEPRECATION")
                    override fun onServiceFound(info: NsdServiceInfo) {
                        try { nsd.resolveService(info, resolver) } catch (_: Exception) {}
                    }
                }

                nsd.discoverServices("_syncbridge._tcp.", NsdManager.PROTOCOL_DNS_SD, discovery)
                cont.invokeOnCancellation { stop() }
            }
        }
}
