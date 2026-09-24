package app.build

import org.gradle.api.Project
import org.gradle.api.plugins.PluginManager

class ConventionPlugin(private val pluginManager: PluginManager) {
    fun apply(target: Project) {
        with(pluginManager) {
            apply("com.android.application")
        }
    }

    fun applyKotlin() {
        pluginManager.apply {
            apply("org.jetbrains.kotlin.android")
        }
    }
}
