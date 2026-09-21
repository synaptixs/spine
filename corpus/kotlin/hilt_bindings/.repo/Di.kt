package shop.di

import dagger.Binds
import dagger.Lazy
import dagger.Module
import dagger.Provides
import javax.inject.Named

interface CartRepository {
    fun items(): List<String>
}

class OfflineCartRepository : CartRepository

class FakeCartRepository : CartRepository

class Clock

@Module
interface DataModule {
    @Binds
    fun bindsCartRepository(impl: OfflineCartRepository): CartRepository

    @Binds
    @Named("fake")
    fun bindsFakeCartRepository(impl: FakeCartRepository): CartRepository
}

@Module
object ClockModule {
    @Provides
    fun providesClock(): Clock = Clock()

    @Provides
    fun providesLazyClock(): Lazy<Clock> = TODO()

    @Provides
    fun providesClockSet(): Set<Clock> = emptySet()
}

class StrayBinding {
    @Binds
    fun bindsClock(impl: Clock): Clock = impl
}

class CartViewModel(private val repository: CartRepository) {
    fun load() {
        repository.items()
    }
}
