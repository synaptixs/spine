package svc;

interface A {
    void ping();
}

interface B {
    void ping();
}

// Inherits ping from two unrelated interfaces at one level.
interface Both extends A, B {
}
