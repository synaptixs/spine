package shop.nested

class Order {
    class Line {
        fun again(): Order {
            return Order()
        }
    }

    class Batch {
        fun first(): Line {
            return Line()
        }

        class Deep {
            fun deep(): Line {
                return Line()
            }
        }
    }

    fun add(): Line {
        return Line()
    }
}

class Cart {
    class Box

    fun pack(): Box {
        return Box()
    }
}

class Box

class Shelf {
    fun stock(): Box {
        return Box()
    }

    fun reorder(): Order {
        return Order()
    }
}

fun qualified(): Order.Line {
    return Order.Line()
}
