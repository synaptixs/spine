package shop.ui

import shop.data.Topic
import shop.util.slugify

class Screen(private val t: Topic) {
    fun show(): String = t.slugify()
}
