package com.syncbridge.app

data class FileMeta(val size: Long, val mtime: Double, val sha256: String)

/** One step of the plan the laptop returns. sha256/size = the laptop's current copy (null if the laptop doesn't have it). */
data class PlanAction(val kind: String, val path: String, val sha256: String?, val size: Long?)

data class SyncResult(val actions: Int, val errors: List<String>)
