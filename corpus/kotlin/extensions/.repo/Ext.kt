package shop.text

import shop.remote.scaled
import shop.remote.tapped
import shop.remote.trim

class Slug(val raw: String)

class Formatter {
    fun run(slug: Slug) {
        slug.tidy()
        slug.trim()
        shorten(slug)
    }

    fun sizes(n: Int, big: Long): Long {
        n.scaled()
        return big.scaled()
    }

    fun tap(slug: Slug): Slug = slug.tapped()
}

fun Slug.tidy(): String = raw

fun shorten(slug: Slug): String = slug.raw
