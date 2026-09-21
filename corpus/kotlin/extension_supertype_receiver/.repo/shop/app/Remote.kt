package shop.app

import shop.tx.commit
import vendor.RemoteLedger

class Remote(private val ledger: RemoteLedger) {
    fun save(): Int = ledger.commit()
}
