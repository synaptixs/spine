using Microsoft.AspNetCore.Mvc;

namespace Svc;

// An in-repo class that shares its name with a property ControllerBase declares.
public class User
{
    public static string Find(string claim) => claim;
}

public class Home : ControllerBase
{
    // `User` is the inherited framework property here, not the class above.
    public string Get() => User.Find("sub");
}
