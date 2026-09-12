package Shop::Cart;
use parent -norequire, 'Shop::Base';

sub total {
    my $self = shift;
    return $self->SUPER::helper();
}

sub new {
    my $class = shift;
    my $self = $class->SUPER::new(@_);
    return $self;
}

1;
