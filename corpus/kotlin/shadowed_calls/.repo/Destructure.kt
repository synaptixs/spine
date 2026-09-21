package shop.scope

class Screen {

    fun show(m: Map<String, () -> Unit>) {
        for ((name, key) in m) {
            key()
        }
    }

    fun key(): String = "k"

    fun render(items: List<Pair<String, String>>) {
        for ((tag, path) in items) {
            tag(path)
        }
    }

    fun tag(p: String): String = p
}
