package app.ui

import app.data.Topic
import app.util.format
import app.util.render

class Screen(private val t: Topic) {
    fun show() {
        t.format()
        t.render()
    }
}
