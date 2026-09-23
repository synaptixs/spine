import React from 'react';
import { Title } from './Title';

function greet(name) {
  return "Hello " + name;
}

export function App() {
  return (
    <div className="app">
      <Title text={greet("x")} />
    </div>
  );
}
