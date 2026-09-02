package main

import "github.com/gin-gonic/gin"

func listOrders(c *gin.Context) {}

func main() {
	r := gin.Default()

	// A group is a router whose prefix is its parent's plus its own.
	v1 := r.Group("/v1")
	v1.GET("/orders", listOrders)

	// An inline handler: an endpoint, but no symbol to hang EXPOSES on.
	v1.POST("/orders", func(c *gin.Context) {})

	r.GET("/health", listOrders)

	// CONTROL: a path held in a variable must yield nothing at all.
	path := "/dynamic"
	r.GET(path, listOrders)
}
