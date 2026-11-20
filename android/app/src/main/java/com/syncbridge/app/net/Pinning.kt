package com.syncbridge.app.net

import okhttp3.OkHttpClient
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * Certificate pinning for the laptop's self-signed certificate.
 *
 * The SHA-256 fingerprint arrives out-of-band in the pairing QR code. The TLS handshake
 * itself is rejected unless the server's leaf certificate matches it, so a stranger on shared
 * Wi-Fi cannot impersonate the laptop. No CA is trusted at all, and hostname checks are
 * skipped because the pin is a stronger identity check than the IP address.
 */
object Pinning {
    fun sha256Hex(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    private fun trustManager(fp: String) = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {
            throw CertificateException("client certificates are not supported")
        }

        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            if (chain.isEmpty()) throw CertificateException("empty certificate chain")
            val actual = sha256Hex(chain[0].encoded)
            if (!actual.equals(fp, ignoreCase = true)) {
                throw CertificateException("Pinned certificate mismatch (possible impersonation)")
            }
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    fun client(fp: String): OkHttpClient {
        val tm = trustManager(fp)
        val ctx = SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), SecureRandom()) }
        return OkHttpClient.Builder()
            .sslSocketFactory(ctx.socketFactory, tm)
            .hostnameVerifier { _, _ -> true }
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .build()
    }
}
