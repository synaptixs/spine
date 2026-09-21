package shop.remote

import shop.text.Slug

fun Slug.trim(): String = raw

fun Slug.tidy(): String = raw

fun Int.scaled(): Int = this

fun Long.scaled(): Long = this

fun <T> T.tapped(): T = this
