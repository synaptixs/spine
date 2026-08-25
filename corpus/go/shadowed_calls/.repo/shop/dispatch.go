package shop

func Handle() int {
	return 1
}

func Run(Handle func() int) int {
	return Handle()
}

func Direct() int {
	return Handle()
}
