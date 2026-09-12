package Shop::Log;

sub new {
    my ($class) = @_;
    return bless {}, $class;
}

sub write {
    return 1;
}

package Shop::Cart;

sub via_literal {
    my $log = Shop::Log->new;
    $log->write;
}

sub via_param {
    my ($self, $thing) = @_;
    $thing->write;
}
