namespace App;

public class Dispatch {
    public int Handle() {
        return 1;
    }

    public int Run(System.Func<int> Handle) {
        return Handle();
    }

    public int Direct() {
        return Handle();
    }
}
