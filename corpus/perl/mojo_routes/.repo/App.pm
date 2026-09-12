package MyApp;
use Mojo::Base 'Mojolicious';

sub startup {
    my $self = shift;
    my $r = $self->routes;

    $r->get('/orders')->to('orders#index');
    $r->get('/orders/closure')->to(sub {
        return 1;
    });
    $r->any('/orders/anything')->to('orders#index');

    my $api = $r->under('/api');
    $api->get('/orders')->to(controller => 'orders', action => 'index');
}
