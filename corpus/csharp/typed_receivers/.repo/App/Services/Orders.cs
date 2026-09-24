using App.Contracts;
using App.Data;
using App.Util;

namespace App.Services;

public class Orders : BaseRepo
{
    private readonly IStore _store2;

    public Cache Cache { get; }

    public Orders(IStore store, Cache cache)
    {
        _store2 = store;
        Cache = cache;
    }

    public int ViaField(int id) => _store2.Load(id);

    public void ViaProperty() => Cache.Clear();

    public int ViaParameter(Store s) => s.Load(1);

    public int ViaLocal()
    {
        Store local = new Store();
        return local.Count();
    }

    public int ViaVarNew()
    {
        var v = new Store();
        return v.Count();
    }

    public int ViaInheritedField() => _store.Count();

    public int ViaThis() => this._store2.Load(2);

    public string ViaStatic() => Fmt.Money(3);

    public int ViaInheritedMember(SpecialStore sp) => sp.Count();
}
