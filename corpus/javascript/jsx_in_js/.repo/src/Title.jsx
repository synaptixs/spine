export const Title = ({ text }) => <h1>{format(text)}</h1>;

function format(value) {
  return value.trim();
}
