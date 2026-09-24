using System.Collections.Generic;
using App.Model;
using App.Util;
using Timer = System.Threading.Timer;

namespace App.Svc
{
    public class Maker
    {
        public void Plain() { var o = new Order(); }

        public void WithArgs() { Order o = new Order(7); }

        public void Qualified() { var o = new App.Model.Order(); }

        public void Nested() { var line = new Order.Line(); }

        public void Generic() { var b = new Box<Order>(); }

        public void Initializer() { var o = new Order { Id = 3 }; }

        public void TargetTyped() { Order o = new(); }

        public void Struct() { var p = new Point(1); }

        public void Record() { var m = new Money(2m); }

        public Order Returned() => new Order();

        public Order TargetTypedReturn() { return new(); }

        public void Array() { var a = new Order[3]; }

        public void ExternalGeneric() { var l = new List<Order>(); }

        public void Shadow<Order>() where Order : new() { var x = new Order(); }

        public void Aliased() { var t = new Timer(null); }
    }
}
