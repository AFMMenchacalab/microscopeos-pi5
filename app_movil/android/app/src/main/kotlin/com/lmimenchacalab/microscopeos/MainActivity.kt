package com.lmimenchacalab.microscopeos

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * Pide los permisos de Bluetooth (buscar y conectarse a un microscopio).
 * flutter_reactive_ble no los pide solo; así no hace falta otro paquete.
 * Lo llama lib/api/bluetooth/bluetooth.dart por el canal "microscopeos/permisos".
 */
class MainActivity : FlutterActivity() {
    private var pendiente: MethodChannel.Result? = null

    private fun permisos(): Array<String> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            // Hasta Android 11, buscar por Bluetooth exige permiso de ubicación.
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }

    private fun concedidos() = permisos().all {
        checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "microscopeos/permisos")
            .setMethodCallHandler { llamada, resultado ->
                if (llamada.method != "bluetooth") {
                    resultado.notImplemented()
                } else if (concedidos()) {
                    resultado.success(true)
                } else {
                    pendiente?.success(false)
                    pendiente = resultado
                    requestPermissions(permisos(), PEDIDO)
                }
            }
    }

    override fun onRequestPermissionsResult(codigo: Int, permisos: Array<out String>, concedidos: IntArray) {
        super.onRequestPermissionsResult(codigo, permisos, concedidos)
        if (codigo == PEDIDO) {
            pendiente?.success(concedidos.isNotEmpty() && concedidos.all { it == PackageManager.PERMISSION_GRANTED })
            pendiente = null
        }
    }

    companion object {
        private const val PEDIDO = 4201
    }
}
