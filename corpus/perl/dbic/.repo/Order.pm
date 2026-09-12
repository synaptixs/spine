package App::Schema::Result::Order;
use base 'DBIx::Class::Core';

__PACKAGE__->table('orders');
__PACKAGE__->add_columns(qw(id customer_id));
__PACKAGE__->set_primary_key('id');

__PACKAGE__->belongs_to(customer => 'App::Schema::Result::Customer', 'customer_id');
__PACKAGE__->has_many(items => 'App::Schema::Result::OrderItem', 'order_id');

1;
