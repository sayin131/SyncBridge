package com.syncbridge.app

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()

    private val scanLauncher = registerForActivityResult(ScanContract()) { result ->
        result.contents?.let { vm.onQr(it) }
    }
    private val folderLauncher = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        uri?.let { vm.onFolder(it) }
    }
    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 33) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)

        setContent {
            MaterialTheme {
                Screen(
                    vm = vm,
                    onScan = {
                        scanLauncher.launch(
                            ScanOptions()
                                .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                                .setPrompt("Scan the QR code shown in the SyncBridge PC app")
                                .setBeepEnabled(false)
                                .setOrientationLocked(false),
                        )
                    },
                    onPickFolder = { folderLauncher.launch(null) },
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Screen(vm: MainViewModel, onScan: () -> Unit, onPickFolder: () -> Unit) {
    val ui by vm.ui.collectAsState()
    val running by vm.running.collectAsState()
    val log by vm.log.collectAsState()

    Scaffold(topBar = { TopAppBar(title = { Text("SyncBridge") }) }) { padding ->
        Column(
            Modifier.padding(padding).padding(16.dp).fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            ui.message?.let { msg ->
                Card(Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(msg, Modifier.weight(1f))
                        TextButton(onClick = vm::dismissMessage) { Text("OK") }
                    }
                }
            }

            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Laptop", style = MaterialTheme.typography.titleMedium)
                    if (ui.paired) {
                        Text("Paired with ${ui.laptop}")
                        TextButton(onClick = vm::unpair) { Text("Unpair") }
                    } else {
                        Text("Open the SyncBridge PC app and scan the QR code it shows.")
                        Button(onClick = onScan) { Text("Scan QR to pair") }
                    }
                }
            }

            if (ui.paired) {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Sync folder", style = MaterialTheme.typography.titleMedium)
                        Text(ui.folder ?: "No folder chosen")
                        OutlinedButton(onClick = onPickFolder) { Text(if (ui.folder == null) "Choose folder" else "Change folder") }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("Auto-sync on Wi-Fi")
                                Text("Live while the app is open, every ~15 min otherwise", style = MaterialTheme.typography.bodySmall)
                            }
                            Switch(checked = ui.autoSync, onCheckedChange = vm::setAutoSync)
                        }
                        Button(onClick = vm::syncNow, enabled = !running, modifier = Modifier.fillMaxWidth()) {
                            Text(if (running) "Syncing…" else "Sync now")
                        }
                        if (running) LinearProgressIndicator(Modifier.fillMaxWidth())
                    }
                }
            }

            Text("Activity", style = MaterialTheme.typography.titleMedium)
            LazyColumn(Modifier.weight(1f)) {
                items(log.asReversed()) { line -> Text(line, style = MaterialTheme.typography.bodySmall) }
            }
        }
    }
}
