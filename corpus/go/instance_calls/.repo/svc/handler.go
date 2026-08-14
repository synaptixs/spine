package svc

type Handler struct{}

func (h Handler) Format() string {
	return "ok"
}

func (h Handler) Run() string {
	return h.Format()
}

func Dispatch(h Handler) string {
	return h.Run()
}
