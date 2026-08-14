CREATE TABLE audit_log (
    id INT PRIMARY KEY,
    note VARCHAR(255)
);

CREATE FUNCTION log_note(n VARCHAR(255)) RETURNS INT AS $$
BEGIN
    INSERT INTO audit_log (note) VALUES (n);
    RETURN 1;
END;
$$ LANGUAGE plpgsql;

CREATE PROCEDURE record_twice(n VARCHAR(255)) AS $$
BEGIN
    CALL log_note(n);
    CALL log_note(n);
END;
$$ LANGUAGE plpgsql;
