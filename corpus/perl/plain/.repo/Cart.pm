package Shop::Cart;
use parent -norequire, 'Shop::Base';
use Shop::Tax qw(rate);

sub new {
    my ($class) = @_;
    return bless {}, $class;
}

sub total {
    my $self = shift;
    $self->helper();
    rate();
    Shop::Tax::rate();
    return 0;
}

sub helper {
    return 1;
}
