package App::Schema::Result::OrderItem;
use base 'DBIx::Class::Core';

__PACKAGE__->table('order_items');
__PACKAGE__->add_columns(qw(id order_id));
__PACKAGE__->set_primary_key('id');

__PACKAGE__->belongs_to('order', 'App::Schema::Result::Order', 'order_id');

1;
