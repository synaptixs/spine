namespace App.Model
{
    public class Order
    {
        public Order() { }

        public Order(int id) { }

        public int Id { get; set; }

        public class Line { }
    }

    public class Box<T> { }

    public struct Point
    {
        public Point(int x) { }
    }

    public record Money(decimal Amount);

    public class Special : Order
    {
        public Special() : base(1) { }
    }
}
