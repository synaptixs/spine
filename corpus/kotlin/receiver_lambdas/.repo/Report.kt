package app.text

class Report {
    fun append(line: String) {}

    fun render(): String = buildString {
        append("header")
    }
}
