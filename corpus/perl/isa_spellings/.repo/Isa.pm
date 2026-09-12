package Shop::A;
use parent -norequire, 'Shop::Base1';

package Shop::B;
our @ISA = ('Shop::Base2');

package Shop::C;
extends 'Shop::Base3';

package Shop::D;
use Mojo::Base 'Shop::Base4';

package Shop::E;
our @ISA = (compute_base());

use v5.38;
class Shop::F :isa(Shop::Base6) {
}
