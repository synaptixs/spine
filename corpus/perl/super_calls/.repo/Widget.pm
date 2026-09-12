package Shop::Widget;
use parent -norequire, 'Vendor::External';

sub build {
    my $self = shift;
    return $self->SUPER::build();
}

1;
