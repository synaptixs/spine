package shop

// Pricer is satisfied structurally — Go has no `implements` keyword.
type Pricer interface {
	Subtotal() int
}

type Cart struct {
	Currency string
	items    []int
}

func (c Cart) Subtotal() int {
	return len(c.items)
}

func Rate() int {
	return 20
}

func Total() int {
	return Rate()
}
