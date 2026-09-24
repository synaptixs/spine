package svc;

interface Proto {
    int settings();
}

abstract class AbstractProto implements Proto {
    public int settings() {
        return 1;
    }
}

// Both supertypes declare settings(), but AbstractProto overrides Proto's: not a tie.
class XProto extends AbstractProto implements Proto {
}
