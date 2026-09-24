package app.data

import app.util.log
import org.gradle.api.plugins.PluginManager

class TopicRepository {
    fun refresh() {}

    fun apply(id: String) {}
}

class Sync(private val repo: TopicRepository, private val pluginManager: PluginManager) {
    fun refresh() {}

    fun helper() {}

    fun apply(id: String) {}

    fun viaReceiver() {
        with(repo) { refresh() }
    }

    fun fallsBack() {
        with(repo) { helper() }
    }

    fun innermostWins() {
        with(repo) {
            with(pluginManager) { apply("x") }
        }
    }

    fun controlRun() {
        run { helper() }
    }

    fun controlLet() {
        repo.let { helper() }
    }

    fun controlAlso() {
        repo.also { helper() }
    }

    fun controlImported() {
        with(pluginManager) { log("applied") }
    }
}
