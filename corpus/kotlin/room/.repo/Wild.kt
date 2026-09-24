package shop.wild

import androidx.room.Entity

@Entity(tableName = "wild_things")
class WildEntity(val id: String)

@Entity
class MysteryThing(val id: String)

@Entity
class WildDto(val id: String)
