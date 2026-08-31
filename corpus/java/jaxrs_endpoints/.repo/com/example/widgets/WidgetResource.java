package com.example.widgets;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;

@Path("/widgets")
public class WidgetResource {
    @GET
    @Path("/{id}")
    public String getWidget() {
        return "ok";
    }
}
