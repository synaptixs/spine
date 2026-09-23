<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Customer extends Model
{
}

class OrderItem extends Model
{
}

class Order extends Model
{
    public function customer()
    {
        return $this->belongsTo(Customer::class);
    }

    public function items()
    {
        return $this->hasMany(OrderItem::class);
    }

    public function notification()
    {
        return $this->belongsTo(\Vendor\Notifications\DatabaseNotification::class);
    }
}
