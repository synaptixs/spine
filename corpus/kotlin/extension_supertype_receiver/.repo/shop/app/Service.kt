package shop.app

import shop.db.Ledger
import shop.tx.commit

class Service(private val ledger: Ledger) {
    fun save(): Int = ledger.commit()
}
