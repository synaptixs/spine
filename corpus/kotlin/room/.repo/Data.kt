package shop.db

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Upsert
import com.vendor.MysteryThing

@Entity(tableName = "topics")
data class TopicEntity(
    @PrimaryKey val id: String,
    val name: String,
)

@Entity(
    tableName = "news_topics",
    foreignKeys = [
        ForeignKey(
            entity = TopicEntity::class,
            parentColumns = ["id"],
            childColumns = ["topic_id"],
        ),
    ],
)
data class NewsTopicCrossRef(
    @PrimaryKey val topic_id: String,
)

@Dao
interface TopicDao {
    @Query(value = "SELECT * FROM topics")
    fun all(): List<TopicEntity>

    @Query(
        value = """
        DELETE FROM topics
        WHERE id = :id
    """,
    )
    suspend fun remove(id: String)

    @Upsert
    suspend fun upsert(entities: List<TopicEntity>)

    @Insert
    fun insertMystery(dto: MysteryThing)
}
