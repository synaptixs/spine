package app.ui

import androidx.compose.ui.Modifier
import java.io.BufferedReader
import java.lang.Runnable

class Screen(private val m: Modifier, private val r: BufferedReader, private val task: Runnable) {
    fun draw() {
        m.let { }
        m.run { }
        m.takeIf { true }
        r.use { }
        m.also { }
        task.run()
    }
}
