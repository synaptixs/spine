using App.Contracts;

namespace App.Data;

// The interface lives in a sibling namespace, reached only through the `using` above.
public class Store : IStore
{
    public int Load(int id) => id;

    public int Count() => 1;
}
