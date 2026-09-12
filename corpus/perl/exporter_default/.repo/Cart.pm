package Shop::Cart;
use Shop::Util;
use Carp;

sub total {
    fmt();
    croak('bad');
    return 0;
}
